from django.db import transaction
from django.utils import timezone
from .models import Order, Trade
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


def match_order(new_order):   
    print("match")
    # changes
    closing_price= None
    # Begin a transaction to ensure atomicity
    with transaction.atomic():
        # For a BUY limit order, we are looking for SELL orders at the same price or lower
        if new_order.order_type == 'BUY' and new_order.order_mode == 'LIMIT':
            opposite_orders = Order.objects.filter(
                order_type='SELL', 
                order_mode='LIMIT', 
                price__lte=new_order.price, 
                is_matched=False
            ).order_by('price', 'timestamp')
            broadcast_orderbook_update()
        
        # For a SELL limit order, we are looking for BUY orders at the same price or higher
        elif new_order.order_type == 'SELL' and new_order.order_mode == 'LIMIT':
            opposite_orders = Order.objects.filter(
                order_type='BUY', 
                order_mode='LIMIT', 
                price__gte=new_order.price, 
                is_matched=False
            ).order_by('-price', 'timestamp')
            broadcast_orderbook_update()

        # For a BUY market order, we are looking for SELL orders with the lowest price
        elif new_order.order_type == 'BUY' and new_order.order_mode == 'MARKET':
            opposite_orders = Order.objects.filter(
                order_type='SELL', 
                is_matched=False
            ).order_by('price', 'timestamp')
            broadcast_orderbook_update()

        # For a SELL market order, we are looking for BUY orders with the highest price
        elif new_order.order_type == 'SELL' and new_order.order_mode == 'MARKET':
            opposite_orders = Order.objects.filter(
                order_type='BUY', 
                is_matched=False
            ).order_by('-price', 'timestamp')
            broadcast_orderbook_update()

        # Immediate or Cancellation (IOC) orders
        if new_order.is_ioc:
            # Track executed quantity for IOC orders
            executed_quantity=0
            
            for opposite_order in opposite_orders:
                if new_order.quantity<=0:
                    break
                
                match_quantity=min(new_order.quantity,opposite_order.quantity)
                
                closing_price= opposite_order.price
                # Create a trade entry for the matched orders
                Trade.objects.create(
                    buyer=new_order.user if new_order.order_type == 'BUY' else opposite_order.user,
                    seller=opposite_order.user if new_order.order_type == 'BUY' else new_order.user,
                    quantity=match_quantity,
                    price=closing_price,
                    timestamp=timezone.now()
                )
                broadcast_orderbook_update()
                
                # Update quantities
                executed_quantity += match_quantity
                new_order.quantity -= match_quantity
                opposite_order.quantity -= match_quantity

                broadcast_orderbook_update()
                
                # Update disclosed quantity if needed
                if(opposite_order.disclosed>opposite_order.quantity):# < to > Akshat
                    opposite_order.disclosed=opposite_order.quantity
                if(new_order.disclosed>new_order.quantity):# < to > Akshat
                    new_order.disclosed=new_order.quantity
                
                broadcast_orderbook_update()

            
                
                # Update opposite order status
                if opposite_order.quantity == 0:
                    opposite_order.is_matched = True
                opposite_order.save()
                broadcast_orderbook_update()
            
            # Handle IOC order after matching
            if executed_quantity>0:
                # Partially executed:save with executed quantity and mark as matched
                new_order.quantity=0  # Discard remaining quantity
                new_order.is_matched=True
                new_order.disclosed=0 
                print("saved1")
                new_order.save()
                broadcast_orderbook_update()
                return  # To prevent further processing
            else:
                # Completely unexecuted:delete the order
                print("delete1")
                new_order.delete()
                broadcast_orderbook_update()
                return

        # Try to match with the opposite orders
        if(new_order.order_mode=="LIMIT"):
            remaining_quantity = new_order.quantity
            for opposite_order in opposite_orders:
                if remaining_quantity <= 0:
                    break
                
                match_quantity = min(remaining_quantity, opposite_order.quantity)
                # closing_price = opposite_order.price if new_order.order_mode == 'LIMIT' else opposite_order.price
                if new_order.order_mode == 'LIMIT':
                    match_price = opposite_order.price
                # For market orders, the price is taken from the best available order
                else:
                    match_price = opposite_order.price
        
                # Create a trade entry for the matched orders
                Trade.objects.create(
                    buyer=new_order.user if new_order.order_type == 'BUY' else opposite_order.user,
                    seller=opposite_order.user if new_order.order_type == 'BUY' else new_order.user,
                    quantity=match_quantity,
                    price= match_price,
                    timestamp=timezone.now()
                )
                broadcast_orderbook_update

                # Update the quantities of the matched orders
                remaining_quantity -= match_quantity
                opposite_order.quantity -= match_quantity
                new_order.quantity -= match_quantity
                if(opposite_order.disclosed>opposite_order.quantity):# < to > Akshat
                    opposite_order.disclosed=opposite_order.quantity
                if(new_order.disclosed>new_order.quantity):# < to > Akshat
                    new_order.disclosed=new_order.quantity
                opposite_order.save()
                new_order.save()
                broadcast_orderbook_update()

                # If the opposite order is fully matched, mark it as matched
                if opposite_order.quantity == 0:
                    opposite_order.is_matched = True
                    opposite_order.save()
                    broadcast_orderbook_update()

                # If the new order is fully matched, mark it as matched
                if new_order.quantity == 0:
                    new_order.is_matched = True
                    new_order.save()
                    broadcast_orderbook_update()

            # If the new order is partially matched, update its quantity and status
            if new_order.quantity > 0:
                new_order.save()
                broadcast_orderbook_update()
            else:
                new_order.is_matched = True
                new_order.save()
                broadcast_orderbook_update()

            # Ensure that any remaining unmatched orders are still available for future matches
            
            new_order.timestamp = timezone.now()
            # process_stoploss_orders(closing_price)
            new_order.save()
            broadcast_orderbook_update()
        else:
            
            remaining_quantity=new_order.quantity
            complete_order=False
            # while(remaining_quantity>0):
            try:
                for opposite_order in opposite_orders:
                    if(remaining_quantity<=0):
                        complete_order=True
                        break
                    match_quantity=min(opposite_order.quantity,remaining_quantity)
                    # closing_price=opposite_order.price
                    if(match_quantity==opposite_order.quantity):
                        Trade.objects.create(
                            buyer=new_order.user if new_order.order_type == 'BUY' else opposite_order.user,
                            seller=opposite_order.user if new_order.order_type == 'BUY' else new_order.user,
                            quantity=match_quantity,
                            price=opposite_order.price,
                            timestamp=timezone.now()
                        )
                        broadcast_orderbook_update()
                        remaining_quantity-=match_quantity
                        opposite_order.quantity -= match_quantity
                        new_order.quantity -= match_quantity
                        if(opposite_order.disclosed>opposite_order.quantity):# < to > Akshat
                            opposite_order.disclosed=opposite_order.quantity
                        if(new_order.disclosed>new_order.quantity):# < to > Akshat
                            new_order.disclosed=new_order.quantity
                        opposite_order.is_matched = True
                        opposite_order.save()
                        broadcast_orderbook_update()
                        new_order.save()
                        broadcast_orderbook_update()
                    else:
                        Trade.objects.create(
                            buyer=new_order.user if new_order.order_type == 'BUY' else opposite_order.user,
                            seller=opposite_order.user if new_order.order_type == 'BUY' else new_order.user,
                            quantity=match_quantity,
                            price=opposite_order.price,
                            timestamp=timezone.now()
                        )
                        broadcast_orderbook_update()
                        remaining_quantity-=match_quantity
                        opposite_order.quantity -= match_quantity
                        new_order.quantity -= match_quantity
                        if(opposite_order.disclosed>opposite_order.quantity):# < to > Akshat
                            opposite_order.disclosed=opposite_order.quantity
                        if(new_order.disclosed>new_order.quantity):# < to > Akshat
                            new_order.disclosed=new_order.quantity
                        # if(match_quantity == opposite_order.quantity):
                        #     opposite_order.is_matched = True
                        new_order.is_matched=True
                        opposite_order.save()
                        broadcast_orderbook_update()
                        new_order.save()
                        broadcast_orderbook_update()
            except Exception as e:
                print('Some Error Occured')
            
            if(complete_order==False):
                #the leftover quantity is converted to 0
                remaining_quantity=0
                new_order.quantity=0
                new_order.is_matched=True
                new_order.save()
                broadcast_orderbook_update()
                print("Incomplete order Placed")
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

def broadcast_orderbook_update():
    from .models import Order, Trade

    buy_orders = Order.objects.filter(order_type='BUY', is_matched=False).order_by('-price')
    sell_orders = Order.objects.filter(order_type='SELL', is_matched=False).order_by('price')
    recent_trades = Trade.objects.order_by('-timestamp')[:10]

    best_bid = buy_orders.first()
    best_ask = sell_orders.first()

    payload = {
        'best_bid': {
            'price': float(best_bid.price),
            'disclosed': best_bid.disclosed,
        } if best_bid else None,
        'best_ask': {
            'price': float(best_ask.price),
            'disclosed': best_ask.disclosed,
        } if best_ask else None,
        'buy_orders': [
            {
                'price': float(o.price),
                'disclosed': o.disclosed,
            } for o in buy_orders
        ],
        'sell_orders': [
            {
                'price': float(o.price),
                'disclosed': o.disclosed,
            } for o in sell_orders
        ],
        'trades': [
            {
                'buyer': t.buyer.username,
                'seller': t.seller.username,
                'price': float(t.price),
                'quantity': t.quantity,
                'timestamp': t.timestamp.isoformat(),
            } for t in recent_trades
        ]
    }


    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        'orderbook_group',
        {
            'type': 'send_order_update',
            'payload': payload,
        }
    )
    print("Orderbook updated and broadcasted")
    # return payload